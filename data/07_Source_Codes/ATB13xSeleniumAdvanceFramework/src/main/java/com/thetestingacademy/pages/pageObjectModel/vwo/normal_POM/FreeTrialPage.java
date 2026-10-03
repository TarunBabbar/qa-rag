package com.thetestingacademy.pages.pageObjectModel.vwo.normal_POM;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;

public class FreeTrialPage {

    public FreeTrialPage(WebDriver driver) {
        this.driver = driver;
    }

    WebDriver driver;

    private By email_input = By.id("page-v1-step1-email");
    private By checkbox = By.id("page-free-trial-step1-cu-gdpr-consent-checkbox");
    private By submitButton = By.xpath("//button[text()=\"Create a Free Trial Account\"]");
    private By error_msg_xpath = By.xpath("//div[contains(@class,\"invalid-reason\")]");

    public String enterDetailsInvalid(String email) {
        driver.get("https://vwo.com/free-trial/");
        driver.findElement(email_input).sendKeys(email);
        driver.findElement(checkbox).click();
        driver.findElement(submitButton).click();
        String error_msg = driver.findElement(error_msg_xpath).getText();
        return error_msg;

    }


}
